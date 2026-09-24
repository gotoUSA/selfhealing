"""
포인트 장부 계약 — 잔액(User.points)과 적립 건별 남은 양이 어느 경로로도 갈라지지 않는다

2026-09-24 실제 스택 검증에서 나온 것:
- 고객용 POST /api/points/cancel/ 이 "내 canceled 주문인가"만 보고 원하는 금액을 원하는 만큼 환불해 줬다
  (포인트 1,003,000P를 만들어 포인트 전액 결제까지 됐다)
- 쓴 포인트 환불이 잔액만 올리고 적립 건은 "다 씀"으로 둬서, 주문하고 취소하면 만료 직전 포인트가
  만료 없는 포인트가 됐다
- 만료 배치 전체가 트랜잭션 하나라 한 건의 DB 에러가 그날 만료분 전체를 되돌렸는데 결과는 성공이었다
- 만료 예정 화면이 원래 적립액을 더해, 다 쓴 포인트를 "만료 예정"으로 보여줬다
"""

from datetime import timedelta

import pytest
from django.db import connection
from django.db.models import F
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from rest_framework import status
from shopping.models.order import Order, OrderItem
from shopping.models.point import PointHistory
from shopping.models.product import Product
from shopping.services.order_service import OrderService
from shopping.services.point_service import PointExpiryIncompleteError, PointService

USED = 1000


def _only_ledger_points(user, earns):
    """잔액을 적립 건으로만 채운다. earns = [(포인트, 만료까지 일수)]"""
    user.__class__.objects.filter(pk=user.pk).update(points=0)
    rows = []
    for points, days in earns:
        PointService.add_points(user=user, amount=points, type="earn", description="적립")
        row = PointHistory.objects.filter(user=user, type="earn").order_by("-id").first()
        PointHistory.objects.filter(pk=row.pk).update(expires_at=timezone.now() + timedelta(days=days))
        rows.append(row.pk)
    user.refresh_from_db()
    return rows


def _processed_order(user, product, used_points=USED):
    """주문 처리 태스크가 끝난 주문: 아이템 + 재고 차감 + FIFO 포인트 차감('use' 이력)"""
    order = Order.objects.create(
        user=user,
        status="confirmed",
        total_amount=product.price,
        used_points=used_points,
        final_amount=product.price - used_points,
        shipping_name="홍길동",
        shipping_phone="010-1234-5678",
        shipping_postal_code="12345",
        shipping_address="서울시 강남구",
        shipping_address_detail="101동",
    )
    OrderItem.objects.create(
        order=order,
        product=product,
        product_name=product.name,
        quantity=1,
        price=product.price,
    )
    Product.objects.filter(pk=product.pk).update(stock=F("stock") - 1)
    assert PointService().use_points_fifo(user=user, amount=used_points, type="use", order=order)["success"]
    return order


def _move_expiry_to_past(pk):
    PointHistory.objects.filter(pk=pk).update(expires_at=timezone.now() - timedelta(minutes=1))


def _remaining(pk):
    """적립 건 남은 양 — 저장된 사용량으로 직접 계산 (계산 함수와 무관하게)"""
    row = PointHistory.objects.get(pk=pk)
    return row.points - (row.metadata or {}).get("used_amount", 0)


@pytest.mark.django_db
class TestCustomerPointApisRemoved:
    """고객이 포인트를 직접 환불·사용하는 API 는 없다 — 환불은 취소·반품 서비스 안에서만"""

    def test_points_cancel_route_is_gone(self, authenticated_client, user, product):
        _only_ledger_points(user, [(USED, 365)])
        order = _processed_order(user, product)
        OrderService.cancel_order(order)
        user.refresh_from_db()
        before = user.points

        response = authenticated_client.post(
            "/api/points/cancel/",
            {"order_id": order.id, "amount": 1_000_000, "type": "cancel_refund"},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        user.refresh_from_db()
        assert user.points == before

    def test_points_use_route_is_gone(self, authenticated_client, user):
        _only_ledger_points(user, [(USED, 365)])

        response = authenticated_client.post("/api/points/use/", {"amount": 500}, format="json")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        user.refresh_from_db()
        assert user.points == USED

    @pytest.mark.parametrize("name", ["point_cancel", "point_use"])
    def test_route_names_are_gone(self, name):
        with pytest.raises(NoReverseMatch):
            reverse(name)


@pytest.mark.django_db
class TestRefundRestoresEarnRows:
    """쓴 포인트 환불은 적립 건과 원래 만료일까지 되돌린다"""

    def test_order_cancel_gives_the_points_back_with_their_original_expiry(self, user, product):
        (earn,) = _only_ledger_points(user, [(USED, 1)])
        order = _processed_order(user, product)
        assert _remaining(earn) == 0

        OrderService.cancel_order(order)

        user.refresh_from_db()
        assert user.points == USED
        assert _remaining(earn) == USED  # 잔액 = 적립 건 남은 양

    def test_cancel_does_not_turn_expiring_points_into_points_that_never_expire(self, user, product):
        (earn,) = _only_ledger_points(user, [(USED, 1)])
        order = _processed_order(user, product)
        OrderService.cancel_order(order)

        _move_expiry_to_past(earn)
        PointService().expire_points()

        user.refresh_from_db()
        assert user.points == 0

    def test_refund_of_points_whose_expiry_already_passed_expires_them_at_once(self, user, product):
        (earn,) = _only_ledger_points(user, [(USED, 1)])
        order = _processed_order(user, product)
        _move_expiry_to_past(earn)
        PointService().expire_points()  # 다 써서 만료될 게 없다

        OrderService.cancel_order(order)

        user.refresh_from_db()
        assert user.points == 0
        refund = PointHistory.objects.get(order=order, type="cancel_refund")
        expire = PointHistory.objects.get(order=order, type="expire")
        assert refund.points == USED and expire.points == -USED
        # 배치가 다시 돌아도 두 번 만료시키지 않는다
        PointService().expire_points()
        user.refresh_from_db()
        assert user.points == 0

    def test_split_refunds_restore_each_earn_row_once(self, user, product):
        """나눠 한 반품: 되돌린 양의 합이 쓴 양을 넘지 않는다 (나중에 만료되는 적립 건부터 되돌림)"""
        soon, late = _only_ledger_points(user, [(600, 10), (400, 100)])
        order = _processed_order(user, product)
        service = PointService()

        first = service.refund_used_points(user=user, amount=500, order=order)
        second = service.refund_used_points(user=user, amount=500, order=order)

        assert [(d["history_id"], d["amount"]) for d in first["restored"]] == [
            (late, 400),
            (soon, 100),
        ]
        assert [(d["history_id"], d["amount"]) for d in second["restored"]] == [(soon, 500)]
        assert _remaining(soon) == 600 and _remaining(late) == 400
        user.refresh_from_db()
        assert user.points == USED

    def test_points_that_were_not_earn_rows_come_back_as_balance_only(self, user, product):
        """적립 외 포인트(관리자 지급 등)로 쓴 몫은 원래도 만료일이 없었다 — 잔액만 돌려준다"""
        user.__class__.objects.filter(pk=user.pk).update(points=0)
        PointService.add_points(user=user, amount=USED, type="admin_add")
        order = _processed_order(user, product)

        result = PointService().refund_used_points(user=user, amount=USED, order=order)

        assert result["restored"] == [] and result["unbacked"] == USED
        user.refresh_from_db()
        assert user.points == USED


@pytest.mark.django_db
class TestExpiryBatchIsOneTransactionPerRow:
    """한 적립 건의 DB 에러가 다른 건의 만료를 되돌리지 않고, 결과가 성공으로 남지 않는다"""

    def test_one_failing_row_does_not_undo_the_others(self, user_factory, monkeypatch):
        users = [user_factory(username=f"expiry{i}", points=0) for i in range(3)]
        rows = [_only_ledger_points(u, [(USED, 1)])[0] for u in users]
        for pk in rows:
            _move_expiry_to_past(pk)

        original = PointHistory.create_history.__func__

        def fail_for_second_user(cls, user, *args, **kwargs):
            if kwargs.get("type") == "expire" and user.pk == users[1].pk:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1/0")  # 실제 DB 에러 — PostgreSQL 이 그 트랜잭션을 aborted 로 만든다
            return original(cls, user, *args, **kwargs)

        monkeypatch.setattr(PointHistory, "create_history", classmethod(fail_for_second_user))

        with pytest.raises(PointExpiryIncompleteError) as exc:
            PointService().expire_points()

        assert exc.value.expired_count == 2
        assert exc.value.failed_history_ids == [rows[1]]
        balances = [type(u).objects.get(pk=u.pk).points for u in users]
        assert balances == [0, USED, 0]


@pytest.mark.django_db
class TestExpiringDisplayCountsWhatIsLeft:
    """만료 예정 = 남은 양 (만료 안내 메일과 같은 계산)"""

    def test_spent_points_are_not_shown_as_expiring(self, authenticated_client, user, product):
        _only_ledger_points(user, [(USED, 20)])
        _processed_order(user, product, used_points=300)

        my = authenticated_client.get(reverse("my_points")).data
        expiring = authenticated_client.get(reverse("expiring_points"), {"days": 30}).data

        assert my["point_info"]["expiring_soon"] == 700
        assert expiring["total_expiring"] == 700
        assert [m["points"] for m in expiring["monthly_summary"]] == [700]

    def test_fully_spent_earn_row_is_not_listed(self, authenticated_client, user, product):
        _only_ledger_points(user, [(USED, 20)])
        _processed_order(user, product)

        expiring = authenticated_client.get(reverse("expiring_points"), {"days": 30}).data

        assert expiring["total_expiring"] == 0
        assert expiring["histories"] == []
        assert expiring["monthly_summary"] == []

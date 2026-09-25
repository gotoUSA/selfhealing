"""
목록·상세 응답의 쿼리 수가 행 수와 무관한지(N+1), 상품 카드 필드가 빠지지 않는지

같은 엔드포인트를 행 3개인 사용자와 12개인 사용자로 불러 쿼리 수가 같아야 한다.
2026-09-25 실측(수정 전): 장바구니 3곳·주문 상세 +3/행, 재고 부족·찜 목록 +2/행, 상품 상세 +1/리뷰,
카테고리 목록 +1/행(3단 이상이면 +1 더). 장바구니·주문 상세·재고 부족은 상품 카드의 평점·리뷰 수·찜 수·찜 여부가
응답에서 빠져 있었다 (스키마에는 필수 필드).

쿼리 수로는 안 보이는 과다 적재는 SQL 모양으로 확인한다: 목록이 상품마다 리뷰 전체를 prefetch 하던 것
(상품 12개 × 리뷰 2천 개 = 요청당 2만 4천 행, 쿼리 수는 그대로), 인기·평점순이 리뷰를 다시 JOIN 해
서브쿼리 값까지 GROUP BY 하던 것 (리뷰 2만 4천 개에서 5초).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection
from django.db.models import Avg
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from shopping.models.cart import Cart, CartItem
from shopping.models.order import OrderItem
from shopping.models.product import Category, Product, ProductImage, ProductReview
from shopping.models.user import User
from shopping.serializers.product_serializers import ProductListSerializer
from shopping.tests.factories import OrderFactory

SMALL, BIG = 3, 12
CARD_FIELDS = set(ProductListSerializer().fields)


def _user(name: str, **extra) -> User:
    return User.objects.create_user(
        username=name,
        email=f"{name}@test.com",
        password="testpass123",
        is_email_verified=True,
        **extra,
    )


def _actor(tag: str, n: int, reviewers: list[User], root: Category) -> dict:
    """행 n개짜리 판매자·구매자: 상품 n개(이미지 2·리뷰 2·찜 2), 장바구니 n개, 찜 n개, 주문 1건(항목 n개)"""
    seller = _user(f"seller_{tag}", is_seller=True)
    buyer = _user(f"buyer_{tag}")
    category = Category.objects.create(name=f"카테고리 {tag}", slug=f"cat-{tag}", parent=root)
    products = []
    for i in range(n):
        product = Product.objects.create(
            name=f"{tag} 상품 {i}",
            slug=f"{tag}-{i}",
            category=category,
            seller=seller,
            price=Decimal("10000"),
            stock=5,  # 재고 부족(10개 이하) 목록에도 나온다
            sku=f"SKU-{tag}-{i}",
            description="설명",
        )
        for j in range(2):
            ProductImage.objects.create(product=product, image=f"products/{tag}-{i}-{j}.png", order=j)
        for reviewer in reviewers[:2]:
            ProductReview.objects.create(product=product, user=reviewer, rating=4, comment="좋아요")
        product.wished_by_users.add(reviewers[0], buyer)
        products.append(product)

    # 상세 대상 상품: 리뷰 n개 (최근 리뷰는 최대 10개까지 보인다)
    focus = products[0]
    for reviewer in reviewers[2:n]:
        ProductReview.objects.create(product=focus, user=reviewer, rating=5, comment="최고")

    cart = Cart.objects.create(user=buyer, is_active=True)
    for product in products:
        CartItem.objects.create(cart=cart, product=product, quantity=1)

    order = OrderFactory(user=buyer, status="delivered")
    for product in products:
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            quantity=1,
            price=product.price,
        )

    return {
        "tag": tag,
        "seller": seller,
        "buyer": buyer,
        "category": category,
        "focus": focus,
        "order": order,
    }


@pytest.fixture
def world(db):
    reviewers = [_user(f"reviewer{i}") for i in range(BIG)]
    root = Category.objects.create(name="루트", slug="root")
    return {
        "small": _actor("small", SMALL, reviewers, root),
        "big": _actor("big", BIG, reviewers, root),
    }


def _get(user: User | None, url: str):
    """한 번 데워 두고(콘텐츠 타입 캐시 등) 두 번째 요청의 쿼리를 잰다"""
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    client.get(url)
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(url)
    assert response.status_code == 200, response.content[:500]
    return ctx.captured_queries, response.json()


CASES = [
    ("product-list", "buyer", lambda a: f"/api/products/?seller={a['seller'].id}"),
    ("product-search", "buyer", lambda a: f"/api/products/search/?q={a['tag']}"),
    (
        "category-products",
        "buyer",
        lambda a: f"/api/categories/{a['category'].id}/products/",
    ),
    ("low-stock", "seller", lambda a: "/api/products/low_stock/"),
    ("product-detail", "buyer", lambda a: f"/api/products/{a['focus'].id}/"),
    ("order-detail", "buyer", lambda a: f"/api/orders/{a['order'].id}/"),
    ("cart", "buyer", lambda a: "/api/cart/"),
    ("cart-items-action", "buyer", lambda a: "/api/cart/items/"),
    ("cart-items-viewset", "buyer", lambda a: "/api/cart-items/"),
    ("wishlist", "buyer", lambda a: "/api/wishlist/"),
]


@pytest.mark.django_db
class TestQueryCountDoesNotGrowWithRows:
    """행 3개와 12개에서 쿼리 수가 같다"""

    @pytest.mark.parametrize("who, url", [(c[1], c[2]) for c in CASES], ids=[c[0] for c in CASES])
    def test_same_query_count_for_3_and_12_rows(self, world, who, url):
        small, _ = _get(world["small"][who], url(world["small"]))
        big, _ = _get(world["big"][who], url(world["big"]))

        assert len(big) == len(small), f"{len(small)} -> {len(big)} queries:\n" + "\n".join(q["sql"][:160] for q in big)

    def test_category_list_query_count_independent_of_rows_and_depth(self):
        """카테고리 목록: 하위 수·전체 경로를 행마다 따로 읽지 않는다 (3단 트리)"""
        root = Category.objects.create(name="가전", slug="appliance")
        mid = Category.objects.create(name="컴퓨터", slug="computer", parent=root)
        for i in range(2):
            Category.objects.create(name=f"노트북 {i}", slug=f"laptop-{i}", parent=mid)
        few, few_body = _get(None, "/api/categories/")

        for i in range(2, 8):
            Category.objects.create(name=f"노트북 {i}", slug=f"laptop-{i}", parent=mid)
        many, many_body = _get(None, "/api/categories/")

        assert (len(few_body["results"]), len(many_body["results"])) == (4, 10)
        assert len(many) == len(few), "\n".join(q["sql"][:160] for q in many)

        rows = {row["name"]: row for row in many_body["results"]}
        assert rows["노트북 3"]["full_path"] == "가전 > 컴퓨터 > 노트북 3"
        assert rows["컴퓨터"]["children_count"] == 8
        assert rows["가전"]["children_count"] == 1


def _expected_card(product: Product, viewer: User) -> dict:
    avg = product.reviews.aggregate(v=Avg("rating"))["v"]
    return {
        "average_rating": round(float(avg), 1) if avg is not None else 0.0,
        "review_count": product.reviews.count(),
        "wishlist_count": product.wished_by_users.count(),
        "is_wished": product.wished_by_users.filter(pk=viewer.pk).exists(),
    }


CARD_CASES = [
    (
        "product-list",
        "buyer",
        lambda a: f"/api/products/?seller={a['seller'].id}",
        lambda b: b["results"][0],
    ),
    ("low-stock", "seller", lambda a: "/api/products/low_stock/", lambda b: b[0]),
    (
        "order-detail",
        "buyer",
        lambda a: f"/api/orders/{a['order'].id}/",
        lambda b: b["order_items"][0]["product_info"],
    ),
    ("cart", "buyer", lambda a: "/api/cart/", lambda b: b["items"][0]["product"]),
    (
        "cart-items-action",
        "buyer",
        lambda a: "/api/cart/items/",
        lambda b: b[0]["product"],
    ),
    (
        "cart-items-viewset",
        "buyer",
        lambda a: "/api/cart-items/",
        lambda b: b[0]["product"],
    ),
]


@pytest.mark.django_db
class TestProductCardFields:
    """상품 카드(ProductListSerializer)를 그리는 모든 곳에서 필드가 전부, 실제 값으로 나온다"""

    @pytest.mark.parametrize(
        "who, url, pick",
        [(c[1], c[2], c[3]) for c in CARD_CASES],
        ids=[c[0] for c in CARD_CASES],
    )
    def test_nested_card_has_every_field_with_real_values(self, world, who, url, pick):
        viewer = world["big"][who]
        _, body = _get(viewer, url(world["big"]))
        card = pick(body)

        assert set(card) == CARD_FIELDS
        product = Product.objects.get(pk=card["id"])
        assert {k: card[k] for k in ("average_rating", "review_count", "wishlist_count", "is_wished")} == _expected_card(
            product, viewer
        )

    def test_single_item_response_has_every_field(self, world):
        """장바구니 담기 응답(단건)도 필드가 빠지지 않는다 — 미리 계산하지 않은 상품은 직접 조회"""
        buyer = _user("single_buyer")
        product = world["big"]["focus"]
        client = APIClient()
        client.force_authenticate(user=buyer)

        response = client.post(
            "/api/cart/add_item/",
            {"product_id": product.id, "quantity": 1},
            format="json",
        )

        assert response.status_code == 201, response.content[:500]
        card = response.json()["item"]["product"]
        assert set(card) == CARD_FIELDS
        assert {k: card[k] for k in ("average_rating", "review_count", "wishlist_count", "is_wished")} == _expected_card(
            product, buyer
        )


def _review_selects(queries) -> list[str]:
    return [q["sql"] for q in queries if q["sql"].startswith('SELECT "shopping_productreview"')]


@pytest.mark.django_db
class TestNoReviewOverfetch:
    """리뷰는 서브쿼리로만 센다 — 리뷰 행 전체를 읽거나 리뷰를 JOIN 해 GROUP BY 하지 않는다"""

    def test_product_list_reads_no_review_rows(self, world):
        queries, _ = _get(world["big"]["buyer"], f"/api/products/?seller={world['big']['seller'].id}")

        assert _review_selects(queries) == []

    def test_product_detail_reads_only_the_recent_reviews(self, world):
        queries, body = _get(world["big"]["buyer"], f"/api/products/{world['big']['focus'].id}/")

        selects = _review_selects(queries)
        assert len(selects) == 1 and "LIMIT 10" in selects[0], selects
        assert len(body["recent_reviews"]) == 10
        assert body["review_count"] == BIG

    @pytest.mark.parametrize("url", ["/api/products/popular/", "/api/products/best_rating/"])
    def test_ranking_does_not_join_reviews(self, world, url):
        queries, body = _get(None, url)

        assert body, "ranking should list the seeded products"
        assert not [q["sql"] for q in queries if 'JOIN "shopping_productreview"' in q["sql"]]

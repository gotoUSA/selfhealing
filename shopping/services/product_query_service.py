"""
상품 카드 조회 모양 (읽기 전용 쿼리)

ProductListSerializer 는 상품마다 판매자·카테고리 이름, 대표 이미지, 평균 평점·리뷰 수·찜 수·내 찜 여부를
읽는다. 이 값들을 상품마다 따로 조회하면 목록 한 번에 쿼리가 행 수만큼 늘어난다 (N+1).
상품 목록뿐 아니라 장바구니·주문 상세·재고 부족 목록처럼 상품 카드를 그리는 곳은 모두 이 모듈의 쿼리셋으로
상품을 가져온다 — 그러면 쿼리 수가 행 수와 무관하고, 네 통계 값이 응답에서 빠지지 않는다.
"""

from __future__ import annotations

from typing import Any

from django.db.models import (
    Avg,
    BooleanField,
    Count,
    Exists,
    FloatField,
    IntegerField,
    OuterRef,
    Prefetch,
    QuerySet,
    Subquery,
    Value,
)
from django.db.models.functions import Coalesce
from shopping.models.product import Product, ProductReview


def wishlist_count_subquery() -> Coalesce:
    """상품을 찜한 사용자 수 (상관 서브쿼리, 없으면 0)"""
    wishes = Product.wished_by_users.through.objects.filter(product=OuterRef("pk"))
    return Coalesce(
        Subquery(
            wishes.order_by().values("product").annotate(v=Count("id")).values("v"),
            output_field=IntegerField(),
        ),
        0,
    )


def annotate_list_stats(queryset: Any, user_id: int | None) -> Any:
    """
    상품 목록에 평균 평점·리뷰 수·찜 수·내 찜 여부를 붙인다 (상품당 정확히 한 행)

    리뷰와 찜을 LEFT JOIN 하고 GROUP BY 하던 이전 방식은 두 가지 문제가 있었다:
    - 상품 × 리뷰 × 찜으로 중간 행이 곱해진다 (상품 2만·리뷰 12만·찜 12만에서 64만 행, 700ms)
    - is_wished 의 CASE 식이 GROUP BY 에 들어가, 내가 찜한 상품을 남도 찜했으면
      (is_wished=True 그룹, False 그룹) 두 행으로 갈라져 목록에 같은 상품이 두 번 나오고
      count() 와 wishlist_cnt 도 틀렸다
    상관 서브쿼리는 상품마다 인덱스 조회 몇 번이고 GROUP BY 가 없어 둘 다 사라진다.

    Args:
        queryset: Product 쿼리셋
        user_id: 현재 사용자 ID (비로그인이면 None → is_wished 는 항상 False)
    """
    reviews = ProductReview.objects.filter(product=OuterRef("pk")).order_by().values("product")
    wishes = Product.wished_by_users.through.objects.filter(product=OuterRef("pk"))

    if user_id is None:
        is_wished = Value(False, output_field=BooleanField())
    else:
        is_wished = Exists(wishes.filter(user_id=user_id))

    return queryset.annotate(
        avg_rating=Subquery(reviews.annotate(v=Avg("rating")).values("v"), output_field=FloatField()),
        review_cnt=Coalesce(
            Subquery(reviews.annotate(v=Count("id")).values("v"), output_field=IntegerField()),
            0,
        ),
        wishlist_cnt=wishlist_count_subquery(),
        is_wished=is_wished,
    )


def product_card_queryset(user_id: int | None) -> QuerySet[Product]:
    """
    ProductListSerializer 가 읽는 모든 값을 쿼리 수 고정으로 가져오는 상품 쿼리셋

    - seller, category: JOIN (select_related)
    - images: 상품 전체에 대해 한 번 prefetch (대표 이미지 = 모델 정렬 기준 첫 장)
    - 평균 평점·리뷰 수·찜 수·내 찜 여부: 상관 서브쿼리 (annotate_list_stats)

    리뷰 본문은 가져오지 않는다. 목록은 리뷰를 보여 주지 않는데, 리뷰를 prefetch 하면 상품마다 리뷰 전체를
    읽는다 (상품 12개 × 리뷰 2천 개면 요청 한 번에 2만 4천 행). 쿼리 수는 그대로라 쿼리 수 검사로는 안 보인다.

    Args:
        user_id: 현재 사용자 ID (비로그인이면 None)
    """
    return annotate_list_stats(
        Product.objects.select_related("seller", "category").prefetch_related("images"),
        user_id,
    )


def prefetch_product_cards(lookup: str, user_id: int | None) -> Prefetch:
    """
    상품을 FK 로 가진 행(장바구니 항목·주문 항목)에 상품 카드 모양을 붙이는 Prefetch

    Args:
        lookup: 상품까지의 경로 (예: "product", "order_items__product")
        user_id: 현재 사용자 ID (비로그인이면 None)
    """
    return Prefetch(lookup, queryset=product_card_queryset(user_id))
